/*********************************************************************
* Software License Agreement (BSD License)
*
*  Copyright (c) 2010, Rice University
*  All rights reserved.
*
*  Redistribution and use in source and binary forms, with or without
*  modification, are permitted provided that the following conditions
*  are met:
*
*   * Redistributions of source code must retain the above copyright
*     notice, this list of conditions and the following disclaimer.
*   * Redistributions in binary form must reproduce the above
*     copyright notice, this list of conditions and the following
*     disclaimer in the documentation and/or other materials provided
*     with the distribution.
*   * Neither the name of the Rice University nor the names of its
*     contributors may be used to endorse or promote products derived
*     from this software without specific prior written permission.
*
*  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
*  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
*  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
*  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
*  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
*  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
*  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
*  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
*  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
*  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
*  POSSIBILITY OF SUCH DAMAGE.
*********************************************************************/

/* Author: Justin Kottinger */

#include "ompl/multirobot/base/ProblemDefinition.h"
#include "ompl/base/goals/GoalState.h"


bool ompl::multirobot::base::PlannerSolution::operator<(const PlannerSolution &b) const
{
    if (!approximate_ && b.approximate_)
        return true;
    if (approximate_ && !b.approximate_)
        return false;
    if (approximate_ && b.approximate_)
        return difference_ < b.difference_;
    // if (optimized_ && !b.optimized_)
    //     return true;
    // if (!optimized_ && b.optimized_)
    //     return false;
    return length_ < b.length_;
}

ompl::multirobot::base::ProblemDefinition::ProblemDefinition(SpaceInformationPtr si): 
    si_(std::move(si)), locked_(false), solutions_(std::make_shared<PlannerSolutionSet>())
{
}

unsigned int ompl::multirobot::base::ProblemDefinition::getIndividualCount() const
{
    return individualCount_;
}

const ompl::base::ProblemDefinitionPtr &ompl::multirobot::base::ProblemDefinition::getIndividual(const unsigned int index) const
{
    if (individualCount_ > index)
        return individuals_[index];
    else
        throw Exception("Subspace index does not exist");
}

void ompl::multirobot::base::ProblemDefinition::addIndividual(const ompl::base::ProblemDefinitionPtr &individual)
{
    if (locked_)
        throw Exception("ProblemDefinition is locked and unable to add another individual");
    individuals_.push_back(individual);
    individualCount_ = individuals_.size();
}

void ompl::multirobot::base::ProblemDefinition::addSolutionPlan(const PlanPtr &path, bool approximate, double difference,
                                     const std::string &plannerName) const
{
    PlannerSolution sol(path);
    if (approximate)
        sol.setApproximate(difference);
    sol.setPlannerName(plannerName);
    addSolutionPlan(sol);
}

void ompl::multirobot::base::ProblemDefinition::addSolutionPlan(const PlannerSolution &sol) const
{
    if (sol.approximate_)
        OMPL_INFORM("ProblemDefinition: Adding approximate solution from planner %s", sol.plannerName_.c_str());
    solutions_->add(sol);
}

ompl::multirobot::base::PlanPtr ompl::multirobot::base::ProblemDefinition::getSolutionPlan() const
{
    return solutions_->getTopSolution();
}

// void ompl::multirobot::base::ProblemDefinition::setStartAndGoalStatesAtIndex(unsigned int index, const ompl::base::State *start, 
//         const ompl::base::State *goal, double threshold)
// {
//     clearStartStatesAtIndex(index);
//     addStartStateAtIndex(index, start);
//     setGoalStateAtIndex(index, goal, threshold);
// }

// void ompl::multirobot::base::ProblemDefinition::setGoalStateAtIndex(unsigned int index, const ompl::base::State *goal, double threshold)
// {
//     clearGoalAtIndex(index);
//     auto gs(std::make_shared<ompl::base::GoalState>(si_->getIndividual(index)));
//     gs->setState(goal);
//     gs->setThreshold(threshold);
//     setGoalAtIndex(index, gs);
// }
